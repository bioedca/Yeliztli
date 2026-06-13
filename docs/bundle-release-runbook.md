# VEP bundle — build & release runbook

How to build, verify, and publish the **VEP consequence bundle** (`vep_bundle.db`). This is the
operator runbook referenced by the release workflow and the bundle release notes.

## 1. Overview

The VEP bundle is an indexed SQLite database of pre-computed variant consequences (gene,
transcript, HGVS, MANE, …) for every site in the genotyping union catalog. It is published as a
GitHub release asset and pinned in
[`bundles/manifest.json`](https://github.com/bioedca/Yeliztli/blob/main/bundles/manifest.json).

- **Asset:** `vep_bundle.db` (uncompressed SQLite, ~340–360 MB)
- **Release tag:** `bundle-v<version>` (e.g. `bundle-v2.0.0`)
- **Manifest URL pattern:** `https://github.com/bioedca/Yeliztli/releases/download/bundle-v<version>/vep_bundle.db`
- **Embedded version:** the build stores `bundle_version` in the DB's `bundle_metadata` table, which the release workflow checks against the manifest.

## 2. Prerequisites

- The genotyping **union catalog** (rsid, chrom, pos on GRCh37), produced by
  [`scripts/build_union_catalog.py`](https://github.com/bioedca/Yeliztli/blob/main/scripts).
- An **Ensembl VEP** run of that catalog producing a VCF (off-repo; pin the Ensembl version).
- `gh` CLI authenticated with permission to manage releases.

## 3. Build the bundle

Run VEP against the union catalog to produce `vep_output.vcf.gz`, then build the SQLite bundle:

```bash
python scripts/build_vep_bundle.py \
  --vep-vcf vep_output.vcf.gz \
  --output vep_bundle.db \
  --ensembl-version 112 \
  --bundle-version v2.0.0
```

The `--bundle-version` you pass is embedded in the DB and **must** match the manifest entry.

## 4. Capture integrity values

```bash
sha256sum vep_bundle.db   # → manifest sha256
stat -c %s vep_bundle.db  # → manifest size_bytes
```

## 5. Update the manifest

Edit the `vep_bundle` entry in `bundles/manifest.json` with the new `version`, `build_date`,
`url` (release-tag pattern above), `sha256`, `size_bytes`, and `min_app_version`.

!!! warning "Keep the version contract consistent"
    The manifest `version`, the release **tag** (`bundle-v<version>`), and the `bundle_version`
    **embedded** in the database must all match — the verification workflow (step 7) rejects
    a release where they don't. Build the bundle with the same `--bundle-version` you put in
    the manifest, and tag the release to match.

## 6. Draft the GitHub release

```bash
gh release create bundle-v2.0.0 --draft \
  --title "VEP bundle v2.0.0" \
  --notes-file docs/release-notes/bundle-v2.0.0.md \
  vep_bundle.db
```

## 7. Verify before publishing

Trigger the
[`bundle-release.yml`](https://github.com/bioedca/Yeliztli/blob/main/.github/workflows/bundle-release.yml)
workflow (`workflow_dispatch`) with `release_tag=bundle-v2.0.0` and `bundle_key=vep_bundle`. It
downloads the draft asset and verifies the **tag ↔ manifest version**, the **SHA-256**, the
**size**, and the **embedded `bundle_version`**. Only proceed if it passes.

## 8. Publish

```bash
gh release edit bundle-v2.0.0 --draft=false
```

## 9. Rollback

If a regression is found after publishing:

1. **Revert the `bundles/manifest.json` change** (point `vep_bundle` back at the prior version)
   in a PR — the app then downloads the previous, known-good bundle.
2. **Do not delete** the GitHub release; instead edit its notes to mark the version superseded,
   so existing references stay valid.
3. Note the rollback in `docs/release-notes/`.

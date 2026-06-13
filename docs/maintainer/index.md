# Maintainer

Operator documentation for **building and releasing Yeliztli's reference-data bundles** and
cutting application releases. If you're not maintaining the project's bundles or releases, you
don't need this section.

- **[Bundle build & release](../bundle-release-runbook.md)** — build, verify, and publish the
  VEP consequence bundle.
- **[LAI bundle](lai-bundle.md)** — the local-ancestry bundle's multi-phase, cluster-based
  build.
- **[App release process](release-process.md)** — cutting an application release and the
  bundle release notes.

## How bundles work

Each downloadable bundle is published as a **GitHub release asset** and pinned in
[`bundles/manifest.json`](https://github.com/bioedca/Yeliztli/blob/main/bundles/manifest.json)
by `version`, `url`, `sha256`, `size_bytes`, and `min_app_version`. The app downloads each
bundle from its manifest URL and verifies the checksum and size before use.

The general release flow for every bundle is the same:

1. **Build** the artifact (scripts in [`scripts/`](https://github.com/bioedca/Yeliztli/tree/main/scripts)).
2. **Capture** its `sha256` and `size_bytes` and update the manifest entry.
3. **Draft** a GitHub release with the artifact attached.
4. **Verify** it with the [`bundle-release.yml`](https://github.com/bioedca/Yeliztli/blob/main/.github/workflows/bundle-release.yml)
   workflow (it checks the tag↔version match, checksum, size, and embedded version) **before
   publishing**.
5. **Publish** the release.

!!! warning "Heavy builds belong on a cluster"
    The large bundles (gnomAD ~16 GB of input, the LAI bundle's multi-hour training) should be
    built on a compute cluster, not a laptop — build in node-local scratch and copy only the
    final artifact back. The LAI runbook documents the SLURM flow.

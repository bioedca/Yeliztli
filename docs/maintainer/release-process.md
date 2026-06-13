# App release process

## Cutting an application release

Application releases are driven by a **semver tag** (e.g. `v0.2.0`). Pushing the tag triggers
[`release.yml`](https://github.com/bioedca/Yeliztli/blob/main/.github/workflows/release.yml),
which runs the full gate before the release is considered good:

- lint (Ruff + ESLint),
- backend tests on Linux **and** macOS,
- frontend (Vitest) tests,
- the native install smoke test,
- the Docker build + health check,
- the cross-browser E2E suite,
- performance benchmarks.

The workflow is a **testing gate** — it does not itself publish artifacts. Treat a tag whose
`release.yml` run is green as releasable.

!!! note "Version bookkeeping"
    The app version lives in `pyproject.toml`. Bundles declare a `min_app_version` in the
    [manifest](https://github.com/bioedca/Yeliztli/blob/main/bundles/manifest.json); bump it when
    a bundle requires a newer app.

## Bundle release notes

Each bundle release ships a note under
[`docs/release-notes/`](https://github.com/bioedca/Yeliztli/tree/main/docs/release-notes),
recording the source dataset, build date, checksum, size, scope, license/attribution, and a
verification + rollback pointer:

- `bundle-v2.0.0.md` — the VEP consequence bundle.
- `lai-bundle-v2.0.0.md` — the local-ancestry bundle (accuracy metrics, reference panel).
- `gnomad-bundle-v1.0.0.md` — the gnomAD allele-frequency bundle.

When you publish a new bundle, add a matching release note and link its rollback section to the
relevant runbook ([VEP](../bundle-release-runbook.md) or
[LAI](lai-bundle.md)). See [Attribution](../attribution.md) for the licensing each bundle
carries.

# External inputs strategy

> **Audience:** maintainers. **Status:** the licensing + sourcing authority for every external dataset, model, and runtime Yeliztli depends on. CLAUDE.md names this file as the gate to check **before bundling anything**; the per-dataset attribution that ships with the product lives in [`NOTICE`](https://github.com/bioedca/Yeliztli/blob/main/NOTICE) and [Attribution](attribution.md), and this document records *why* each input is bundled, downloaded, or user-supplied.

Yeliztli's own code is MIT. Every external input is classified into exactly one of three **distribution postures**, decided by the rule below. The posture — not the science — is what this document governs.

## The bundleable-vs-BYO decision rule

A dataset or model may be **bundled** (redistributed by us as a GitHub Release asset, fetched automatically by the setup wizard) **only if** its license clearly permits third-party redistribution — in practice **CC0 / public-domain or CC-BY** (share-alike honored where required, e.g. CC-BY-SA). The classifier is `classify_pgs_license`-style logic (`backend/annotation/pgs_catalog.py::classify_pgs_license`): CC0/CC-BY → bundleable; everything else → not.

Everything that is **not** clearly redistributable is **BYO** (bring-your-own), in one of two forms:

- **Provider-fetched (`pipeline_pins`)** — auto-downloadable directly from the authoritative provider at install/update time, but **not redistributed by us**. The manifest records the upstream URL + license; the data lands in the user's own `data/` directory. Used when the provider permits free download (often academic/non-commercial) but not third-party redistribution.
- **User-supplied-file ingest (no URL)** — the user must obtain the file themselves (e.g. a login-gated portal) and point Yeliztli at it. No URL is stored anywhere in the repo. Used when the provider gates access behind registration/login or a click-through license.

**GPL tools** (Beagle, the HIBAG R package) are a separate axis: they are **invoked as subprocesses, never imported**, so the GPL boundary never reaches the MIT app code. A GPL *binary* that is itself freely redistributable (Beagle's self-contained JAR) may still be co-vendored as a binary; a GPL *library that must be imported* (R/Bioconductor) is treated as an operator-installed runtime, not bundled.

**Owner posture (A):** bundle CC0/CC-BY only; honor CC-BY-SA share-alike; never bundle non-commercial, author-restricted, login-gated, or GPL-tool-*derived* models. When a license verdict is not yet confirmed against the provider's current terms, the item is marked **[EXT-VERIFY]** and stays BYO until confirmed — never bundled on assumption.

---

## Ledger

| Input | Used for | License | Posture | Notes |
|---|---|---|---|---|
| **1000 Genomes Phase 3 v5a** (bref3) | Wave C imputation reference panel | Open / public (no redistribution restriction) | **Bundleable** | Native build-37; chosen over NYGC 30× (GRCh38-only → liftover risk). Panel URL + redistribution terms **[EXT-VERIFY]** before the SW-C1 release. |
| **Beagle 5.x** (JAR) | LAI phasing (live) + Wave C imputation runtime | GPL | **Co-vendored binary** | Self-contained, freely redistributable JAR; **invoked via `subprocess`, never imported** (already vendored in the LAI bundle). |
| **GLIMPSE** | Wave C advanced engine (SW-C7) | **MIT** (verified 2026-06-17) | **Bundleable** | MIT permits redistribution with copyright+permission notice preserved. Candidate to bundle after the SW-C7 license-check PR confirms model/runtime split. |
| **IMPUTE5** | Wave C advanced engine (SW-C7) | "Freely available for **academic use only**"; non-academic requires a formal license (verified 2026-06-17) | **BYO (provider-fetched / user-supplied)** | No third-party redistribution grant → never bundled. |
| **HIBAG** (R/Bioconductor package) | Wave D HLA imputation engine | **GPL-3** (verified 2026-06-17) | **Operator-installed runtime** | Invoked via an Rscript subprocess, never imported. R ≥ 4 + Bioconductor `HIBAG` is operator-installed; not bundled. |
| **HIBAG pre-fit HLA classifier models** | Wave D HLA calling | Per-model, distributed separately (publication-specific / often non-commercial) | **BYO (user-supplied-file)** | Models are **never bundled**; the user fetches the model for their ancestry. Per-model terms **[EXT-VERIFY]** before any change to this posture. |
| **SpliceAI** (precomputed delta-scores) | SW-F2 splice context layer | Code: PolyForm Strict 1.0.0; **trained models + precomputed scores: CC-BY-NC-4.0** ("academic and not-for-profit use; other use requires a commercial license from Illumina") — distributed via **login-gated** Illumina BaseSpace (verified 2026-06-17) | **BYO (user-supplied-file, no URL)** | Non-commercial + login-gated → not redistributable and not auto-fetchable. Ingest a user-supplied VCF; store no URL. |
| **GTEx v8** eQTL summary stats | SW-F3 regulatory-context badge | Open-access summary statistics (eQTL results redistributable; individual WGS is dbGaP-controlled and NOT used) | **Bundleable** *(or pipeline-built)* | Summary stats only. Either ship the built `gtex_eqtl.db` as a CC-open Release, or leave it pipeline-built; the GRCh38→rsID match is a build step (see SW-F3). |
| **dbNSFP** | In-silico predictors (REVEL, CADD, …) | **Academic / non-commercial** | **BYO (provider-fetched)** | Downloaded directly from the provider into the user's `data/`; not redistributed by us (already the shipped posture). |

The fully-bundled, already-shipped CC0/CC-BY datasets (**gnomAD** CC0, **ClinGen** CC0, **AlphaMissense** CC-BY-4.0, **PharmGKB / FDA-via-PharmGKB** CC-BY-SA-4.0, **PGS Catalog** per-score CC-BY-4.0) are documented in full in [`NOTICE`](https://github.com/bioedca/Yeliztli/blob/main/NOTICE) and [Attribution](attribution.md); their posture (bundleable) follows the same rule and is not repeated here.

---

## Per-input detail

### 1000 Genomes Phase 3 v5a — imputation reference panel (Wave C / SW-C1)

- **Posture: bundleable.** 1000 Genomes genotypes are fully open/public with no redistribution restriction; the phased panel is redistributed as a Beagle `bref3` artifact.
- **Why native b37:** the repo's coordinate system is GRCh37 (`EXPECTED_GENOME_BUILD`). The pre-built Phase 3 v5a panel is natively b37, avoiding the phased-panel liftover that an NYGC 30× (GRCh38-only) panel would force.
- **[EXT-VERIFY] before SW-C1 release:** confirm the exact upstream panel URL and that Phase 3 v5a remains the defensible v1 panel vs HRC/TOPMed for the target ancestries (HRC/TOPMed are access-gated → would be BYO, not bundleable). The science-defensibility check is a `Consensus` item (verification ledger #3) deferred until the connector quota resets.

### Beagle 5.x — phasing + imputation runtime (Wave C / SW-C2)

- **Posture: co-vendored GPL binary.** Beagle is GPL but ships as a self-contained, freely redistributable JAR. It is **invoked via `subprocess.run(["java", …, "-jar", beagle.jar, …])` and never imported**, so the GPL boundary never reaches the MIT app code — identical to its current use in `backend/analysis/lai_runner.py`. The JAR is SHA-pinned inside the LAI tarball's `metadata.json::tool_versions` and is reused for imputation.

### GLIMPSE / IMPUTE5 — advanced imputation engines (Wave C / SW-C7)

- **GLIMPSE — bundleable (MIT, verified).** MIT permits redistribution provided the copyright + permission notice is preserved. It is a candidate to bundle after the SW-C7 PR confirms the runtime/model split and adds the MIT notice to `NOTICE`.
- **IMPUTE5 — BYO (verified academic-only).** "Freely available for academic use only"; non-academic use requires a formal Illumina/Marchini-lab license, and no third-party redistribution is granted. Provider-fetched or user-supplied only — never bundled.

### HIBAG — HLA imputation (Wave D / SW-D1)

- **R package — operator-installed GPL-3 runtime (verified).** The HIBAG R/Bioconductor package is GPL-3. It is driven through a GPL-isolated **Rscript subprocess seam** (the R `.R` script lives outside the Python import path, invoked by path), so nothing GPL is imported. R ≥ 4 + Bioconductor `HIBAG` is a net-new **operator-installed** runtime, detected at runtime (`detect_rscript()`), absence reported by a status route — never bundled, never fatal.
- **Pre-fit classifier models — BYO, never bundled.** HIBAG's pre-trained HLA classifiers are distributed separately (e.g. the HIBAG model repository) with **per-model, often publication-specific / non-commercial** terms. They are **always user-supplied**; the user fetches the model appropriate to their ancestry. Per-model license terms are **[EXT-VERIFY]** and the default posture is BYO regardless.

### SpliceAI — precomputed splice delta-scores (SW-F2)

- **Posture: BYO user-supplied-file ingest, no URL.** SpliceAI's code is under PolyForm Strict 1.0.0; the **trained models and precomputed delta-score files are CC-BY-NC-4.0** ("academic and not-for-profit use; other use requires a commercial license from Illumina") and are distributed through **login-gated** Illumina BaseSpace. Non-commercial + access-gated means the scores are neither redistributable nor auto-fetchable: the user obtains the precomputed file themselves and points Yeliztli at it. **No SpliceAI URL is stored in the repo.** The layer is context-only (a splice-impact badge, never ACMG evidence). Tier semantics (0.2/0.5/0.8) + masked-vs-raw default are a `Consensus` item (ledger #10).

### GTEx v8 — eQTL regulatory context (SW-F3)

- **Posture: bundleable (summary stats) or pipeline-built.** Only the **open-access eQTL summary statistics** are used; the protected individual-level WGS is never touched. The build maps each GRCh38 `variant_id` to its dbSNP rsID via GTEx's WGS lookup table and stores rsID-keyed rows joinable to the app's GRCh37 sample data **without a coordinate liftover**. Either ship the built `gtex_eqtl.db` as a CC-open Release (saves every user re-running the match) or leave it pipeline-built. The GRCh38→GRCh37 rsID-match correctness is verification ledger #11 (already coded; covered by tests).

### dbNSFP — in-silico predictors

- **Posture: BYO provider-fetched.** dbNSFP carries an **academic / non-commercial** license. It is downloaded directly from the provider into the user's own `data/` directory at install/update time and is **not redistributed** by Yeliztli — the posture already in production.

---

## How a new input is added

1. **Classify the license** against the rule above (CC0/CC-BY → bundleable; else BYO). Verify against the provider's *current* license page; record the verified date.
2. **Pick the posture** — bundled Release asset, `pipeline_pins` (provider-fetched), or user-supplied-file ingest. GPL tools go behind a subprocess seam.
3. **Record provenance** — add a `NOTICE` stanza + an [Attribution](attribution.md) row (source URL, license, version/build, accessed date, scope). For BYO items, the "Downloaded from the provider" / user-supplied note.
4. **For science-defensibility** (not licensing), verify the claim with the `Consensus` connector and carry the citation — see the verification ledger in the second-wave plan. Licensing is verified against license text; science is verified against the literature. The two gates are distinct and both must pass.

> When a license verdict cannot be confirmed (provider terms unclear, or a `Consensus`/`[EXT-VERIFY]` flag is still open), the input stays **BYO and un-bundled** until it is confirmed. Never bundle on assumption.

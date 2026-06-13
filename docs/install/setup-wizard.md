# First-time setup wizard

The first time you open Yeliztli, a six-step wizard walks you through configuration and
gets your data ready to analyse.

## Step 1 — Disclaimer

Read and accept the disclaimer: Yeliztli is for **educational and research use only** and
is **not** a diagnostic tool. (The full statement is on the
[Intended use & disclaimers](../intended-use.md) page.)

## Step 2 — Import from backup (optional)

If you have a previous Yeliztli backup (a `.tar.gz` archive), restore it here to bring back
your samples, configuration, and optionally reference databases. Skip this for a fresh
install. See [backup & restore](backup-restore.md).

## Step 3 — Storage path

Choose where Yeliztli stores everything. The default is `~/.yeliztli/`. The wizard shows
available disk space and warns when it is low (a warning under ~10 GB; setup is blocked
under ~5 GB). See [system requirements](system-requirements.md) for why.

## Step 4 — External services

Both are optional and Yeliztli works without them:

- **PubMed contact email** *(recommended)* — NCBI's terms of service ask for a contact email
  for literature lookups; providing one enables PubMed citation fetching for findings.
- **OMIM API key** *(optional)* — enriches gene–disease associations. Request a key at
  [omim.org/api](https://omim.org/api).

These lookups use public identifiers only — never your genotypes (see [Privacy](../privacy.md)).

## Step 5 — Download reference databases

Yeliztli downloads the reference data needed to annotate your variants. Downloads stream
with live progress and are **resumable** — if one is interrupted, a **Resume** button picks
up from the saved partial instead of restarting. A database is only treated as installed
once it is present *and* passes an integrity check, so a half-finished or corrupted download
is reported honestly rather than failing silently during annotation. You can inspect and
repair any database later under **Settings → System Health → Database Health**.

For the full list of what is downloaded, sizes, sources, and licenses, see
**[reference data](reference-data.md)**.

## Step 6 — Upload your data

Upload your raw genotype file. Yeliztli supports **23andMe** (v3/v4/v5) and **AncestryDNA**
(v2.0) exports, as a `.txt` or `.zip`; it auto-detects the vendor and format. Once parsing
finishes, annotation runs automatically in the background, followed by the analysis modules.

!!! tip "Multiple files for one person"
    If you have tested with more than one service, you can add additional files later from
    the Upload page. Files from the same person — for example a 23andMe and an AncestryDNA
    export — can be grouped under one **individual** and optionally combined into a single
    merged sample.

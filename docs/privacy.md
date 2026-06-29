# Privacy & data handling

Yeliztli is built around one principle: **your genome is yours, and it stays on your
machine.**

## What stays local

- **Your raw data file**, the parsed genotypes, every annotation, and every analysis
  finding live only in Yeliztli's local data directory (by default `~/.yeliztli/`) on the
  computer you run it on.
- Yeliztli serves its interface on `localhost` and, by default, binds only to the loopback
  address (`127.0.0.1`), so it is not reachable from other machines on your network.
- **Your genotypes are never uploaded.** There is no analytics, no crash reporting, and no
  outbound transfer of your variant data — your raw file, parsed genotypes, annotations, and
  findings never leave your machine, to Yeliztli's authors or anyone else. Yeliztli *does*
  make a small number of automatic **non-genomic** connections by default (an app-version
  check and a reference-data update check); these send no genotype data, but they do reveal
  that you run Yeliztli (your IP, app version, and timing). They are listed in full below.

## When Yeliztli does use the network

Every connection Yeliztli makes is **non-genomic** — your genotypes are never sent. But not
all of them are user-initiated, so here is the complete accounting.

### Automatic (on by default)

- **App-version check.** When you open the dashboard, Yeliztli asks the GitHub Releases API
  (`api.github.com`) whether a newer Yeliztli version is available. This sends your public IP,
  your exact app version (in the request's `User-Agent`), and the time of the request — usage
  metadata, though no genotype. It powers the "update available" banner.
- **Reference-data update check.** On a cadence set by the `update_check_interval` setting
  (default **daily**), Yeliztli fetches the public bundle manifest from
  `raw.githubusercontent.com` to see whether newer reference data exists. This sends your IP
  and the request timing; no genotype.
- **Genome Browser reference & gene tracks.** When you open the **Genome Browser**, it loads
  the GRCh37/hg19 reference sequence and the RefSeq gene track from the IGV.js project's
  hosted genome registry (third-party servers). Because it fetches the sequence by **region**,
  **the loci and genes you navigate to are observable to those hosts** — there is no genotype
  payload, but the regions you choose to inspect are themselves sensitive. This happens only
  while the Genome Browser is open.

### User-initiated

- **Downloading public reference data.** During first-time setup, and whenever you choose to
  update, Yeliztli downloads reference databases and bundles — ClinVar, gnomAD, the VEP
  bundle, and so on — from their public sources. These are one-way downloads of public data;
  nothing about your sample is sent.
- **Optional citation/enrichment lookups.** If you supply a PubMed contact email or an OMIM
  API key, Yeliztli can fetch literature and gene–disease metadata to enrich findings. These
  requests reference public identifiers (PMIDs, gene symbols), **not** your genotypes, and the
  features work without them.

## Going fully offline

You can avoid the **user-initiated** connections by not supplying the PubMed/OMIM keys and not
starting reference downloads. The **automatic** connections are harder to switch off today:

- The app-version and reference-data update checks have no in-app "off" setting yet —
  `update_check_interval` offers only `startup`, `daily`, and `weekly`, so one of them will
  run when you open the app.
- The Genome Browser reaches the IGV.js hosted genome registry whenever you open it.

Until those become configurable, the reliable way to guarantee a fully offline session is to
**block Yeliztli's network access at the operating-system or firewall level** after setup —
that suppresses all of the automatic checks described above. The core analysis pipeline runs
entirely locally and needs no network once reference data is installed; only the app-version
check, the reference-data update check, and the Genome Browser's reference fetch are affected.

## Access control on your own machine

- Yeliztli ships with **optional authentication**: you can require a PIN or password (stored
  only as a salted `bcrypt` hash) and set a session timeout, which is useful on a shared
  computer.
- Because all data is stored in a local directory, standard operating-system file permissions
  and disk encryption apply — protect that directory the way you would any sensitive file.

## Deleting your data

Your data is just files in the data directory. Deleting a sample from the app removes it (and
any merged children); removing the data directory removes everything. Uninstalling Yeliztli
does not delete your data unless you explicitly ask it to.

!!! note "Use test fixtures for demos"
    When experimenting, capturing screenshots, or filing a bug report, use synthetic or test
    genotype data rather than your real file, so you never share genuine genetic information
    by accident.

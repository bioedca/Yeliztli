# Privacy & data handling

Yeliztli is built around one principle: **your genome is yours, and it stays on your
machine.**

## What stays local

- **Your raw data file**, the parsed genotypes, every annotation, and every analysis
  finding live only in Yeliztli's local data directory (by default `~/.yeliztli/`) on the
  computer you run it on.
- **Application logs stay local, too, but they can reveal analysis metadata.**
  Yeliztli writes structured application logs to `reference.db` (`log_entries`, shown in
  **Settings -> System Health -> Log explorer**) and renders logs to stdout, which installed
  services can capture in `journalctl`, `~/Library/Logs/yeliztli-*.log`, or Docker logs. These
  logs are for troubleshooting and can include sample filenames or local paths, gene symbols,
  variant identifiers such as rsIDs, genomic coordinates, download paths, and error details.
  New structured log entries redact genotype-like fields such as genotypes, diplotypes,
  haplotypes, and `gt` values before they reach the Log explorer or service logs, but older logs
  from earlier Yeliztli versions may still contain those values. Logs can still show what you
  analyzed and where files live. Yeliztli does not currently prune the `log_entries` table
  automatically; service-log retention is controlled by your operating system, launch service, or
  Docker runtime.
- Yeliztli serves its interface on `localhost` and, by default, binds only to the loopback
  address (`127.0.0.1`), so it is not reachable from other machines on your network.
  If you override the bind host to a non-loopback address for remote access, enable
  authentication and set a password first; otherwise anyone who can reach the port can use
  the app and API without credentials. See
  [Exposing Yeliztli to your network](install/configuration.md#exposing-yeliztli-to-your-network).
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
- **Genome Browser reference & gene tracks, when the local browser reference is absent.**
  If the optional local Genome Browser reference bundle is installed, Yeliztli serves the
  GRCh37/hg19 FASTA, FASTA index, and RefSeq BED track from the local data directory. If that
  bundle is missing, opening the **Genome Browser** falls back to the IGV.js project's hosted
  genome registry (third-party servers). Because that fallback fetches sequence by **region**,
  **the loci and genes you navigate to are observable to those hosts** — there is no genotype
  payload, but the regions you choose to inspect are themselves sensitive. This fallback happens
  only while the Genome Browser is open, and the **first** time it is needed Yeliztli shows a
  one-time in-app notice before any third-party reference data is requested.
- **Gene Detail UniProt lookup.** Opening a Gene Detail page (`/genes/{symbol}`) checks the
  local UniProt cache and, on a cache miss or stale cache entry, fetches reviewed human protein
  annotations from `rest.uniprot.org/uniprotkb`. The request includes the gene symbol you are
  viewing and reveals that inspection, your IP address, and request timing to UniProt/EBI. It
  does **not** send genotypes, variants, sample IDs, or findings. Results are cached locally in
  `reference.db` for 30 days. If offline or blocked, the page shows stale cached protein data
  when available, or a message that protein data is unavailable.

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

You can avoid the PubMed/OMIM enrichment and reference-download connections by not supplying a
PubMed contact email, not supplying an OMIM key, and not starting reference downloads. For the
**automatic** connections:

- **Turn off the update checks.** Set `update_check_interval = "off"` in your `config.toml`
  (or the `YELIZTLI_UPDATE_CHECK_INTERVAL=off` environment variable). This disables **both** the
  app-version check (`api.github.com`) and the reference-data manifest check
  (`raw.githubusercontent.com`): the daily scheduler becomes a no-op and the dashboard's
  auto-checks return immediately without any outbound request. No update banner will appear; to
  check again, set the value back to `startup`, `daily`, or `weekly`.
- Install the optional local Genome Browser reference bundle (`grch37.fa`,
  `grch37.fa.fai`, `grch37_refseq.bed`, and `genome_browser_reference_manifest.json`) in the
  data directory, or point `YELIZTLI_GRCH37_FASTA_PATH` and
  `YELIZTLI_GENOME_BROWSER_REFSEQ_TRACK_PATH` at the validated local runtime files. When the
  manifest and GRCh37/hg19 FASTA-index sentinels validate, the Genome Browser uses local URLs and
  makes no reference/RefSeq request to IGV.js hosts. If any file is missing or validation fails,
  the Genome Browser keeps the disclosure-gated hosted `hg19` fallback; if you do not open the
  Genome Browser, it makes no connection.
- There is no API-key switch for the Gene Detail UniProt lookup. To avoid that outbound request,
  do not open Gene Detail pages for genes that are not already cached, or block Yeliztli's
  network access. Cached UniProt entries already in `reference.db` can still be shown offline.

For a hard guarantee that **nothing** leaves the machine, **block Yeliztli's network access at the
operating-system or firewall level** after setup — that suppresses all of the automatic checks
above, including the Genome Browser's reference fetch. The core analysis pipeline runs entirely
locally and needs no network once reference data is installed.

## Access control on your own machine

- Yeliztli ships with **optional authentication**: you can require a PIN or password (stored
  only as a salted `bcrypt` hash) and set a session timeout, which is useful on a shared
  computer.
- Authentication is effective only after a password hash exists. Turning on
  `auth_enabled` without setting a password leaves the API open.
- Because all data is stored in a local directory, standard operating-system file permissions
  and disk encryption apply — protect that directory the way you would any sensitive file.

## Deleting your data

Your data is just files in the data directory. Deleting a sample from the app removes it (and
any merged children); removing the data directory removes everything. Uninstalling Yeliztli
does not delete your data unless you explicitly ask it to.

Sample deletion does not rewrite old application logs. Earlier entries in `reference.db` or
service logs may still mention that sample's filename/path or analysis metadata after the sample
itself is gone, and logs from older Yeliztli versions may include genotype-like fields that newer
versions redact. Removing the data directory removes the `reference.db` log table, but service
logs captured outside the data directory must be cleared separately using your operating system,
launch-service, or Docker log controls.

!!! note "Use test fixtures for demos"
    When experimenting, capturing screenshots, or filing a bug report, use synthetic or test
    genotype data rather than your real file, so you never share genuine genetic information
    by accident.

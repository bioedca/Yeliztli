# Privacy & data handling

Yeliztli is built around one principle: **your genome is yours, and it stays on your
machine.**

## What stays local

- **Your raw data file**, the parsed genotypes, every annotation, and every analysis
  finding live only in Yeliztli's local data directory (by default `~/.yeliztli/`) on the
  computer you run it on.
- Yeliztli serves its interface on `localhost` and, by default, binds only to the loopback
  address (`127.0.0.1`), so it is not reachable from other machines on your network.
- There is **no telemetry**, no analytics, no crash reporting, and **no outbound transfer of
  your variant data** — your genotypes are never uploaded to Yeliztli's authors or anyone
  else.

## When Yeliztli does use the network

Yeliztli reaches the internet only for clearly-scoped, **non-genomic** purposes:

- **Downloading public reference data.** During first-time setup (and when you choose to
  update) Yeliztli downloads reference databases and bundles — ClinVar, gnomAD, the VEP
  bundle, and so on — from their public sources. These are one-way downloads of public data;
  nothing about your sample is sent.
- **Optional citation/enrichment lookups.** If you supply a PubMed contact email or an OMIM
  API key, Yeliztli can fetch literature and gene–disease metadata to enrich findings. These
  requests reference public identifiers (PMIDs, gene symbols), **not** your genotypes, and
  the features work without them.

If you want a fully offline experience after setup, you can run Yeliztli without the optional
external-service keys and without triggering updates.

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

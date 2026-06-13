# Yeliztli

**Yeliztli is a privacy-first personal-genomics analysis platform that runs entirely on
your own machine.** Upload the raw data file from a consumer genotyping service
(23andMe or AncestryDNA), and Yeliztli annotates your variants against public clinical
and population databases and organises the results into focused analysis modules —
pharmacogenomics, ancestry, carrier status, hereditary-risk panels, wellness traits, and
more.

Your genome never leaves your computer: Yeliztli runs on `localhost` with **no telemetry,
no cloud processing, and no outbound variant data**.

!!! danger "Read this first — not medical advice"
    Yeliztli is for **research and educational use only**. It analyses consumer
    genotyping-array data, which is **not a clinical-grade test**: results are **not
    diagnostic** and are **not clinically validated**. Do not make medical decisions based
    on them. Please read **[Intended use & disclaimers](intended-use.md)** before you
    begin.

## What you can do with it

- **Annotate** your variants against ClinVar, gnomAD, dbNSFP, VEP, ENCODE, and more.
- **Explore** every variant in an interactive table and an embedded genome browser.
- **Review findings** across many analysis modules, each with an evidence rating and
  source citations.
- **Build custom queries** (visual or SQL) and **export** to VCF, TSV, CSV, JSON, or
  FHIR R4.
- **Generate PDF reports** for the findings you choose.

Everything is opt-in, and the most sensitive modules require an explicit acknowledgement
before any result is shown.

## How this documentation is organised

This site is layered so you can read only the parts you need:

- **Getting started** — install Yeliztli, upload your DNA, and understand your results.
  *(Start here if you just want to use the app.)*
- **Modules & Features** — what each analysis module reports and how to read it, plus the
  variant explorer, genome browser, query builder, reports, and export.
- **Install & self-host** — deeper installation, configuration, reference-data, and
  troubleshooting reference for running your own instance.
- **Develop** — architecture and contributor guide for working on Yeliztli itself.
- **Maintainer** — operator runbooks for building and releasing the reference-data bundles.

!!! note "Documentation in progress"
    This site is being built out section by section. Until every page is published, the
    [project README](https://github.com/bioedca/Yeliztli) remains the quickest route to
    installation and an up-to-date feature list.

## Privacy & licensing

- Yeliztli keeps all processing local — see **[Privacy](privacy.md)**.
- Yeliztli is open source under the MIT license. It bundles and annotates against several
  public datasets, each retained under its own license.

# Yeliztli

**Privacy-first personal-genomics analysis platform — it runs entirely on your own machine.**

Upload the raw data file from a consumer genotyping service (**23andMe** or **AncestryDNA**),
and Yeliztli annotates your variants against public clinical and population databases and
organises the results into focused analysis modules — pharmacogenomics, ancestry, carrier
status, hereditary-risk panels, wellness traits, and more. **Your genome never leaves your
computer**: Yeliztli runs on `localhost` with no telemetry, no cloud processing, and no
outbound variant data.

![The Yeliztli dashboard](docs/assets/img/dashboard.png)

> [!WARNING]
> **Not medical advice.** Yeliztli is for **research and educational use only**. It analyses
> consumer genotyping-array data, which is **not a clinical-grade test** — results are **not
> diagnostic** and **not clinically validated**. Don't make medical decisions based on them.
> See **[Intended use & disclaimers](docs/intended-use.md)**.

## 📖 Documentation

**Full documentation site: <https://bioedca.github.io/Yeliztli/>**

- **[Getting started](docs/getting-started/index.md)** — install, upload your DNA, read your results
- **[Install & self-host](docs/install/index.md)** — native, Docker, configuration, troubleshooting
- **[Module reference](docs/modules/index.md)** — what every analysis module reports and how to read it
- **[Develop](docs/develop/index.md)** — architecture and contributor guide

## Quick start (development)

```bash
git clone https://github.com/bioedca/Yeliztli.git
cd Yeliztli
pip install -e ".[dev]"
cd frontend && npm install && cd ..
make dev
```

Open <http://localhost:5173> — the setup wizard guides you through first-run configuration.
For native services, Docker, and WSL2, see the **[install guide](docs/install/index.md)**.

## Requirements

- **Python 3.12+**, **Node 20+**
- A few GB of free disk for reference data (the setup wizard warns below ~10 GB)
- macOS, Linux, or Windows via **WSL2**; **Java 8+** optional for Tier-2 ancestry

## License & attribution

Yeliztli's code is released under the **[MIT license](LICENSE)**. It annotates against several
public datasets, each retained under its own license — see **[NOTICE](NOTICE)** and the
**[attribution page](docs/attribution.md)**.

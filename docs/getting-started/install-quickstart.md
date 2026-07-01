# Install (quick start)

The fastest way to get Yeliztli running. For full options (Docker, services, WSL2,
configuration), see **[Install & self-host](../install/index.md)**.

You need **Python 3.12+**, **Node 20+**, and enough disk for the reference databases
(~60 GB minimum; ~80 GB recommended) — see
[system requirements](../install/system-requirements.md).

```bash
git clone https://github.com/bioedca/Yeliztli.git
cd Yeliztli
pip install -e .
cd frontend && npm install && npm run build && cd ..
yeliztli-setup install     # registers + starts the background services
```

Then open **[http://localhost:8000](http://localhost:8000)**. The
**[setup wizard](../install/setup-wizard.md)** launches automatically to finish configuration
and download reference data.

!!! note "First-run setup takes a while"
    Full reference-data setup uses more than 60 GB at peak, with ~80 GB recommended for
    headroom, and commonly takes on the order of an hour or more. The dbNSFP step dominates:
    it downloads a large source archive, then builds and indexes a multi-GB SQLite database.
    Slow connections or disks can take considerably longer. This is separate from the later
    per-sample annotation step, which is usually only a few minutes for a standard
    genotyping-array file.

!!! tip "Prefer Docker?"
    `docker compose up -d` runs Yeliztli in containers instead — see [Docker](../install/docker.md).

Next: **[upload your DNA](upload-your-dna.md)**.

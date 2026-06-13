# Install (quick start)

The fastest way to get Yeliztli running. For full options (Docker, services, WSL2,
configuration), see **[Install & self-host](../install/index.md)**.

You need **Python 3.12+**, **Node 20+**, and several GB of free disk space — see
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

!!! tip "Prefer Docker?"
    `docker compose up -d` runs Yeliztli in containers instead — see [Docker](../install/docker.md).

Next: **[upload your DNA](upload-your-dna.md)**.

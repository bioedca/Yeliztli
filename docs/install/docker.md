# Docker Compose

An alternative to the [native install](native-install.md) that runs Yeliztli as containers.

## 1. Build and start

```bash
git clone https://github.com/bioedca/Yeliztli.git
cd Yeliztli
docker compose up -d
```

This starts two services:

- **api** — the FastAPI server on [http://localhost:8000](http://localhost:8000)
- **huey** — the background task worker that runs the annotation pipeline

Your data persists in a Docker volume named `yeliztli-data`.

## 2. Check health

```bash
docker compose ps
curl http://localhost:8000/api/health
```

## 3. View logs

```bash
docker compose logs -f          # all services
docker compose logs -f api      # API server only
docker compose logs -f huey     # task worker only
```

## 4. Stop and restart

```bash
docker compose stop             # stop services
docker compose start            # restart services
docker compose down             # remove containers (data volume preserved)
docker compose down -v          # remove everything, including your data
```

## Use a host directory for data

To store data in a host directory instead of the Docker volume, add an override file:

```yaml
# docker-compose.override.yml
services:
  api:
    volumes:
      - /path/to/your/data:/data
  huey:
    volumes:
      - /path/to/your/data:/data
```

## Environment overrides

All settings can be set via `YELIZTLI_`-prefixed environment variables (see
[configuration](configuration.md)):

```bash
YELIZTLI_PORT=9000 docker compose up -d
```

Or in the override file:

```yaml
services:
  api:
    environment:
      - YELIZTLI_AUTH_ENABLED=true
      - YELIZTLI_LOG_LEVEL=DEBUG
```

After the containers are up, open the app and complete the
**[setup wizard](setup-wizard.md)**.

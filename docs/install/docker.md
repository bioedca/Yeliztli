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

The image includes the Playwright Chromium browser and Linux browser dependencies required
for **Generate PDF** and single-variant evidence-card PDF/PNG exports. If you built an older
image before this support was added, rebuild it with `docker compose up -d --build`.

Your data persists in a Docker volume named `yeliztli-data`.

!!! warning "Keep the published port on loopback unless auth is configured"
    The default Compose file publishes `127.0.0.1:${YELIZTLI_PORT:-8000}:${YELIZTLI_PORT:-8000}`,
    so only the Docker host can reach Yeliztli. Do not set `YELIZTLI_PUBLISH_HOST` to `0.0.0.0`,
    `::`, or a LAN IP unless authentication is enabled **and** a password has been set.
    `YELIZTLI_AUTH_ENABLED=true` by itself is not enough if `YELIZTLI_AUTH_PASSWORD_HASH` is
    empty. For remote access, prefer a TLS-terminating reverse proxy instead of exposing
    uvicorn directly.

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
docker compose down -v          # remove containers and the Docker data volume
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

This is a host bind mount, not a Docker-managed volume. `docker compose down -v` does
not remove `/path/to/your/data`; delete that host directory yourself when you want to
erase those samples.

## Environment overrides

All settings can be set via `YELIZTLI_`-prefixed environment variables (see
[configuration](configuration.md)). When `YELIZTLI_PORT` is set in your shell or `.env`,
it controls both the API process and the published host port in the default Compose file:

```bash
YELIZTLI_PORT=9000 docker compose up -d
curl http://localhost:9000/api/health
```

To publish beyond the Docker host's loopback interface, set `YELIZTLI_PUBLISH_HOST` as well:

```bash
YELIZTLI_PUBLISH_HOST=0.0.0.0 YELIZTLI_PORT=9000 docker compose up -d
```

Or in the override file:

```yaml
services:
  api:
    environment:
      - YELIZTLI_AUTH_ENABLED=true
      - YELIZTLI_PORT=9000
      # Also set a password through the setup wizard or YELIZTLI_AUTH_PASSWORD_HASH.
      - YELIZTLI_LOG_LEVEL=DEBUG
    ports:
      - "127.0.0.1:9000:9000"
```

After the containers are up, open the app and complete the
**[setup wizard](setup-wizard.md)**.

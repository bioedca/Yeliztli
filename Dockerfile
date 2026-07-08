FROM python:3.12-slim

LABEL org.opencontainers.image.title="Yeliztli" \
      org.opencontainers.image.description="Privacy-first personal-genomics analysis platform — runs entirely on your own machine." \
      org.opencontainers.image.source="https://github.com/bioedca/Yeliztli" \
      org.opencontainers.image.url="https://bioedca.github.io/Yeliztli/" \
      org.opencontainers.image.licenses="MIT"

ARG YELIZTLI_PORT=8000

WORKDIR /app

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Install Node.js 20.x for frontend build
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY pyproject.toml README.md ./
COPY backend/ backend/
RUN pip install --no-cache-dir .

# Browser runtime for PDF reports and evidence-card exports. Run as root so
# Playwright can install Debian browser dependencies, but write the browser cache
# under appuser's home so the runtime process can launch Chromium.
RUN HOME=/home/appuser python -m playwright install --with-deps chromium \
    && chown -R appuser:appuser /home/appuser/.cache

# Frontend build
COPY --chown=appuser:appuser frontend/ frontend/
RUN cd frontend && npm install && npm run build

# Create data directory owned by non-root user
RUN mkdir -p /data && chown appuser:appuser /data

# Switch to non-root user
USER appuser

ENV YELIZTLI_HOST=0.0.0.0 \
    YELIZTLI_PORT=${YELIZTLI_PORT}

EXPOSE ${YELIZTLI_PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; port = os.environ.get('YELIZTLI_PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=5)" || exit 1

CMD ["python", "-m", "backend.main"]

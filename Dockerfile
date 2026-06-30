FROM python:3.12-slim

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

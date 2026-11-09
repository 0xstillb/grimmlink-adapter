# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

# Install build dependencies
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir hatchling

# Copy packaging manifests and install package wheels
COPY pyproject.toml README.md /build/
COPY src/ /build/src/

RUN pip wheel --no-deps --no-cache-dir --wheel-dir /build/wheels .

FROM node:22-bookworm-slim AS prose-deps
WORKDIR /converter
COPY prose_converter/package.json prose_converter/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# Runtime stage
FROM python:3.12-slim-bookworm AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ADAPTER_HOST=0.0.0.0 \
    ADAPTER_PORT=8085 \
    SQLITE_DB_PATH=/app/data/grimmlink_adapter.db \
    MIGRATIONS_DIR=/app/migrations \
    PROSE_WORKER_PATH=/app/prose_converter/worker.mjs

# Create non-root user and data volume directory
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r adapter && useradd -r -g adapter -d /app -s /sbin/nologin adapter \
    && mkdir -p /app/data \
    && chown -R adapter:adapter /app

# Copy wheel and install
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

COPY --from=prose-deps /usr/local/bin/node /usr/local/bin/node
COPY --from=prose-deps --chown=adapter:adapter /converter/node_modules /app/prose_converter/node_modules
COPY --chown=adapter:adapter prose_converter/worker.mjs /app/prose_converter/worker.mjs

# Copy migrations with non-root ownership
COPY --chown=adapter:adapter migrations /app/migrations

USER adapter

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8085/healthcheck')" || exit 1

CMD ["python", "-m", "uvicorn", "grimmlink_adapter.main:app", "--host", "0.0.0.0", "--port", "8085"]

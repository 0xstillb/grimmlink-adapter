# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir hatchling

# Copy packaging manifests and install package wheels
COPY pyproject.toml README.md /build/
COPY src/ /build/src/

RUN pip wheel --no-deps --no-cache-dir --wheel-dir /build/wheels .

# Runtime stage
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ADAPTER_HOST=0.0.0.0 \
    ADAPTER_PORT=8085 \
    SQLITE_DB_PATH=/app/data/grimmlink_adapter.db \
    MIGRATIONS_DIR=/app/migrations

# Create non-root user and data volume directory
RUN groupadd -r adapter && useradd -r -g adapter -d /app -s /sbin/nologin adapter \
    && mkdir -p /app/data \
    && chown -R adapter:adapter /app

# Copy wheel and install
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

# Copy migrations with non-root ownership
COPY --chown=adapter:adapter migrations /app/migrations

USER adapter

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8085/healthcheck')" || exit 1

CMD ["python", "-m", "uvicorn", "grimmlink_adapter.main:app", "--host", "0.0.0.0", "--port", "8085"]

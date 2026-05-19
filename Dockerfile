# syntax=docker/dockerfile:1.7
# ---------- Build stage ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies (only what's needed to compile wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first for better layer caching
COPY pyproject.toml ./
COPY README.md LICENSE ./

# Install dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY app/ ./app/

# Install the package and dependencies
RUN pip install --upgrade pip setuptools wheel \
    && pip install .

# ---------- Runtime stage ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HRSOT_DATA_DIR=/data \
    HRSOT_BIND_HOST=0.0.0.0 \
    HRSOT_BIND_PORT=8000

# Install runtime-only dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 hrsot \
    && useradd --system --uid 1000 --gid hrsot --home-dir /app --shell /usr/sbin/nologin hrsot

# Copy the virtualenv from the build stage
COPY --from=builder /opt/venv /opt/venv

# Copy application source
WORKDIR /app
COPY --chown=hrsot:hrsot app/ ./app/
COPY --chown=hrsot:hrsot alembic.ini ./
COPY --chown=hrsot:hrsot alembic/ ./alembic/

# Create the data directory and set ownership
RUN mkdir -p /data && chown -R hrsot:hrsot /data /app

USER hrsot

VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "app.main"]

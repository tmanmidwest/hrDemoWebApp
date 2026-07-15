# syntax=docker/dockerfile:1.7
# HR SoT MCP server — standalone streamable-HTTP gateway image.
# Deliberately independent from the main app image: it ships only the MCP
# package and its small dependency set, and reaches the app over HTTP.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HRMCP_BIND_HOST=0.0.0.0 \
    HRMCP_BIND_PORT=8100 \
    HRMCP_PATH=/mcp

WORKDIR /app

# Install runtime deps (curl for the healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 hrmcp \
    && useradd --system --uid 1000 --gid hrmcp --home-dir /app --shell /usr/sbin/nologin hrmcp

# Dependency layer first for better caching
COPY mcp_server/requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# App source: the package is imported as `mcp_server`, so copy it under that name.
COPY --chown=hrmcp:hrmcp mcp_server/ ./mcp_server/

USER hrmcp

EXPOSE 8100

# The streamable-HTTP endpoint responds to POST; a plain GET returns a fast
# non-5xx (405/400), which is enough to prove the process is up and serving.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS -o /dev/null "http://localhost:${HRMCP_BIND_PORT}${HRMCP_PATH}" \
        || test $? -eq 22

CMD ["python", "-m", "mcp_server"]

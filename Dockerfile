# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    sqlite3 \
    bzip2 \
    gnupg \
    postgresql-client \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && RESTIC_VERSION=0.17.3 \
    && curl -fsSL "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_amd64.bz2" \
       | bunzip2 > /usr/local/bin/restic \
    && chmod 755 /usr/local/bin/restic \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/
COPY src/ /app/src/
COPY server.py /app/

RUN pip install uv && \
    uv venv .venv --python 3.12

RUN --mount=type=secret,id=github_token \
    . .venv/bin/activate && \
    GIT_AUTH="$(cat /run/secrets/github_token)" && \
    uv pip install "burns-logger @ git+https://${GIT_AUTH}@github.com/bradcburns-home/burns-logger@main" && \
    uv pip install --requirement /app/pyproject.toml

# --- Test stage ---
FROM base AS test
COPY tests/ /app/tests/
RUN . .venv/bin/activate && \
    uv pip install pytest pytest-asyncio pytest-mock && \
    pytest tests/ -x -q && \
    touch /test-passed

# --- Final stage ---
FROM base AS final
COPY --from=test /test-passed /tmp/.test-passed

RUN mkdir -p /staging /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD [".venv/bin/python", "server.py", "--port", "8000", "--transport", "streamable-http"]

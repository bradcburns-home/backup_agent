# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    sqlite3 \
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

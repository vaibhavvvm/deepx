# Auto-Dev base image: Python.
# Published as ghcr.io/auto-dev-cli/base-python. The agent's tool calls run
# here; model inference never does (it stays on the host).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates ripgrep coreutils build-essential \
    && rm -rf /var/lib/apt/lists/*

# Common Python tooling most repos expect.
RUN pip install --no-cache-dir uv ruff pytest

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]

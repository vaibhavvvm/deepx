# Auto-Dev base image: polyglot (Python + Node + JDK).
# Published as ghcr.io/auto-dev-cli/base-polyglot. Default for repos that mix
# stacks, e.g. a React frontend + Spring Boot backend.
FROM eclipse-temurin:21-jdk

ENV PYTHONUNBUFFERED=1 \
    NODE_VERSION=20

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates ripgrep coreutils build-essential \
        python3 python3-pip python3-venv maven gradle \
    && rm -rf /var/lib/apt/lists/*

# Node 20 via NodeSource.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]

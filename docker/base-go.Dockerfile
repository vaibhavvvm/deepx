# Auto-Dev base image: Go.
# Published as ghcr.io/auto-dev-cli/base-go.
FROM golang:1.22

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates ripgrep coreutils \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]

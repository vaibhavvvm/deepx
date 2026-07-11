# Auto-Dev base image: Node.js.
# Published as ghcr.io/auto-dev-cli/base-node.
FROM node:20-slim

ENV NODE_ENV=development

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates ripgrep coreutils \
    && rm -rf /var/lib/apt/lists/*

RUN corepack enable

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]

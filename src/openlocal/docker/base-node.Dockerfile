# Auto-Dev base image: Node.js / TypeScript.
# Published as ghcr.io/auto-dev-cli/base-node.
FROM node:22-slim

ENV NODE_ENV=development \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates ripgrep coreutils build-essential \
    && rm -rf /var/lib/apt/lists/*

# Enable corepack for pnpm / yarn without separate install
RUN corepack enable && corepack prepare pnpm@latest --activate

# TypeScript toolchain available globally
RUN npm install -g typescript ts-node tsx eslint prettier

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]

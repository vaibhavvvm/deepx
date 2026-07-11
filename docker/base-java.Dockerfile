# Auto-Dev base image: Java (JDK + Maven + Gradle).
# Published as ghcr.io/auto-dev-cli/base-java. Needed because "add a column to
# the User entity" in a Spring Boot repo requires a real JDK + build tool.
FROM eclipse-temurin:21-jdk

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates ripgrep coreutils maven gradle \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]

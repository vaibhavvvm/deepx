"""Detect a repository's stack and pick a base container image.

The blueprint's insight (7.3): "find the User entity and add a column" in a
Spring Boot repo needs a JDK + Maven, not ``python:3.10-slim``. We sniff marker
files at the repo root and pick the closest published base image, defaulting to
the polyglot image when a repo mixes stacks (e.g. React + Spring Boot).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Published base images (see docker/). Tags are versioned in real releases.
IMAGE_PYTHON = "ghcr.io/auto-dev-cli/base-python:latest"
IMAGE_NODE = "ghcr.io/auto-dev-cli/base-node:latest"
IMAGE_JAVA = "ghcr.io/auto-dev-cli/base-java:latest"
IMAGE_GO = "ghcr.io/auto-dev-cli/base-go:latest"
IMAGE_POLYGLOT = "ghcr.io/auto-dev-cli/base-polyglot:latest"

# Fallback images that exist on Docker Hub, so a fresh machine works before the
# project's own images are published/pulled.
FALLBACK = {
    IMAGE_PYTHON: "python:3.12-slim",
    IMAGE_NODE: "node:20-slim",
    IMAGE_JAVA: "eclipse-temurin:21-jdk",
    IMAGE_GO: "golang:1.22",
    IMAGE_POLYGLOT: "python:3.12-slim",
}

# marker file -> stack name
_MARKERS: dict[str, str] = {
    "package.json": "node",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Pipfile": "python",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "go.mod": "go",
    "Cargo.toml": "rust",  # detected but maps to polyglot for now
}

_STACK_IMAGE = {
    "node": IMAGE_NODE,
    "python": IMAGE_PYTHON,
    "java": IMAGE_JAVA,
    "go": IMAGE_GO,
}


@dataclass
class ImageSelection:
    image: str
    fallback_image: str
    detected_stacks: list[str]
    reason: str


def detect_stacks(repo_root: Path) -> list[str]:
    """Return the distinct stacks whose marker files are present at the root."""
    found: list[str] = []
    for marker, stack in _MARKERS.items():
        if (repo_root / marker).exists() and stack not in found:
            found.append(stack)
    return found


def select_image(repo_root: Path, override: str | None = None) -> ImageSelection:
    """Choose a base image for ``repo_root``.

    ``override`` (from ``.autodev.toml`` ``[sandbox] image``) always wins.
    """
    if override:
        return ImageSelection(
            image=override,
            fallback_image=FALLBACK.get(override, override),
            detected_stacks=detect_stacks(repo_root),
            reason="explicit override from config",
        )

    stacks = detect_stacks(repo_root)
    # "real" stacks we have a dedicated image for.
    known = [s for s in stacks if s in _STACK_IMAGE]

    if len(known) == 0:
        return ImageSelection(
            image=IMAGE_POLYGLOT,
            fallback_image=FALLBACK[IMAGE_POLYGLOT],
            detected_stacks=stacks,
            reason="no recognised stack markers; using polyglot base",
        )
    if len(known) == 1:
        image = _STACK_IMAGE[known[0]]
        return ImageSelection(
            image=image,
            fallback_image=FALLBACK[image],
            detected_stacks=stacks,
            reason=f"detected single stack: {known[0]}",
        )
    return ImageSelection(
        image=IMAGE_POLYGLOT,
        fallback_image=FALLBACK[IMAGE_POLYGLOT],
        detected_stacks=stacks,
        reason=f"polyglot repo: {', '.join(known)}",
    )

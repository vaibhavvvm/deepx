"""Detect a repository's stack and pick a base container image.

The blueprint's insight (7.3): "find the User entity and add a column" in a
Spring Boot repo needs a JDK + Maven, not ``python:3.10-slim``. We sniff marker
files at the repo root and pick the closest published base image, defaulting to
the polyglot image when a repo mixes stacks.

Stack priority (highest to lowest when multiple detected):
  aiml + node  →  polyglot  (AI/ML Python + Node.js/TS combo)
  aiml only    →  aiml      (pure ML/DL/Vision work)
  node only    →  node      (JS / TS projects)
  python only  →  python    (plain Python, no ML markers)
  java / go    →  their own image
  mixed other  →  polyglot
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Published base images (see docker/). Tags are versioned in real releases.
IMAGE_PYTHON = "ghcr.io/openlocal-cli/base-python:latest"
IMAGE_AIML = "ghcr.io/openlocal-cli/base-aiml:latest"
IMAGE_NODE = "ghcr.io/openlocal-cli/base-node:latest"
IMAGE_JAVA = "ghcr.io/openlocal-cli/base-java:latest"
IMAGE_GO = "ghcr.io/openlocal-cli/base-go:latest"
IMAGE_POLYGLOT = "ghcr.io/openlocal-cli/base-polyglot:latest"

# Fallback images from Docker Hub / official registries so a fresh machine
# works before project images are published or pulled.
FALLBACK = {
    IMAGE_PYTHON: "python:3.12-slim",
    IMAGE_AIML: "python:3.12-slim",
    IMAGE_NODE: "node:22-slim",
    IMAGE_JAVA: "eclipse-temurin:21-jdk",
    IMAGE_GO: "golang:1.22",
    IMAGE_POLYGLOT: "python:3.12-slim",
}

# ---------------------------------------------------------------------------
# Marker files → base stack name
# ---------------------------------------------------------------------------
_MARKERS: dict[str, str] = {
    # Node / TypeScript
    "package.json": "node",
    "tsconfig.json": "node",
    # Python (generic)
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Pipfile": "python",
    # AI / ML / DL / Vision specific
    "environment.yml": "aiml",   # conda env — almost always ML
    "train.py": "aiml",
    "model.py": "aiml",
    "dataset.py": "aiml",
    "inference.py": "aiml",
    # Java / Go
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "go.mod": "go",
    "Cargo.toml": "rust",        # detected but maps to polyglot for now
}

# Directories that signal AI/ML work even without top-level marker files.
_AIML_DIRS = ("notebooks", "models", "checkpoints", "weights", "data")

_STACK_IMAGE = {
    "aiml": IMAGE_AIML,
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


def _has_notebooks(repo_root: Path) -> bool:
    """True if any .ipynb file exists directly in the repo root or one level down."""
    if any(repo_root.glob("*.ipynb")):
        return True
    notebooks_dir = repo_root / "notebooks"
    return notebooks_dir.is_dir() and any(notebooks_dir.glob("*.ipynb"))


def detect_stacks(repo_root: Path) -> list[str]:
    """Return the distinct stacks whose marker files/dirs are present."""
    found: list[str] = []

    for marker, stack in _MARKERS.items():
        if (repo_root / marker).exists() and stack not in found:
            found.append(stack)

    # Jupyter notebooks → aiml regardless of other markers
    if "aiml" not in found and _has_notebooks(repo_root):
        found.append("aiml")

    # ML-specific subdirs upgrade plain python → aiml
    if "python" in found and "aiml" not in found:
        if any((repo_root / d).is_dir() for d in _AIML_DIRS):
            found.append("aiml")

    return found


def select_image(repo_root: Path, override: str | None = None) -> ImageSelection:
    """Choose a base image for ``repo_root``.

    ``override`` (from ``.openlocal.toml`` ``[sandbox] image``) always wins.
    """
    if override:
        return ImageSelection(
            image=override,
            fallback_image=FALLBACK.get(override, override),
            detected_stacks=detect_stacks(repo_root),
            reason="explicit override from config",
        )

    stacks = detect_stacks(repo_root)
    known = [s for s in stacks if s in _STACK_IMAGE]

    # AI/ML + Node combo → polyglot (the combined image)
    if "aiml" in known and "node" in known:
        return ImageSelection(
            image=IMAGE_POLYGLOT,
            fallback_image=FALLBACK[IMAGE_POLYGLOT],
            detected_stacks=stacks,
            reason="AI/ML + Node.js detected; using polyglot image",
        )

    # Single recognised stack
    if len(known) == 1:
        image = _STACK_IMAGE[known[0]]
        return ImageSelection(
            image=image,
            fallback_image=FALLBACK[image],
            detected_stacks=stacks,
            reason=f"detected single stack: {known[0]}",
        )

    # plain python + node (no ML markers) → polyglot
    if "python" in known and "node" in known:
        return ImageSelection(
            image=IMAGE_POLYGLOT,
            fallback_image=FALLBACK[IMAGE_POLYGLOT],
            detected_stacks=stacks,
            reason=f"polyglot repo: {', '.join(known)}",
        )

    # Multiple or zero known stacks → polyglot
    reason = (
        f"polyglot repo: {', '.join(known)}"
        if known
        else "no recognised stack markers; using polyglot base"
    )
    return ImageSelection(
        image=IMAGE_POLYGLOT,
        fallback_image=FALLBACK[IMAGE_POLYGLOT],
        detected_stacks=stacks,
        reason=reason,
    )

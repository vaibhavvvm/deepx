"""Layered configuration resolution.

Three layers, later overrides earlier (blueprint section 5):

1. Global   -- ``~/.openlocal/config.toml``
2. Project  -- ``<repo>/.openlocal.toml`` (committed, never contains secrets)
3. Session  -- CLI flags / interactive ``/model`` overrides

Secrets (the Groq key) never live in any of these files -- they live in the OS
keyring or the ``GROQ_API_KEY`` env var (see ``providers/groq_provider.py``).
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

GLOBAL_CONFIG_PATH = Path.home() / ".openlocal" / "config.toml"
PROJECT_CONFIG_NAME = ".openlocal.toml"

# The defaults every layer starts from. Anything here is safe, local, and
# privacy-preserving -- the tool must be fully usable with zero configuration.
DEFAULTS: dict[str, Any] = {
    "model": {
        "default": "ollama:qwen2.5-coder:7b",
        # Optional fallback chain (opt-in). Empty => disabled.
        "fallback": "",
        "fallback_on": ["timeout", "tool_call_parse_error"],
        "fallback_timeout_seconds": 45,
    },
    "sandbox": {
        "image": "",  # "" => auto-detect via image_select
        "network": "none",  # none | restricted | full
        "cpu_limit": "2",
        "memory_limit": "4g",
        "pids_limit": 512,
        "timeout_seconds": 120,
        "keep_alive": False,
        "protect_secret_files": True,
    },
    "policy": {
        "require_approval_for": [
            "git push",
            "rm -rf",
            "npm publish",
            "pip install",
            "curl",
            "wget",
        ],
        "deny": [
            "shutdown",
            "reboot",
            "mkfs",
            "dd if=",
            ":(){:|:&};:",  # fork bomb
            "> /dev/sd",
        ],
    },
    "subagents": {
        "enable_task_delegation": True,
        "max_concurrent": 3,
        "models": {},  # per-role model overrides: {"coder": "groq:..."}
    },
    "telemetry": {
        "enabled": False,
    },
    "tools": {
        # Operating mode:
        #   local  — filesystem/shell tools only (default, fully private)
        #   smart  — + semantic code search via local Ollama embeddings (still private)
        #   web    — legacy mode kept for backwards compat; use plugins.web_search instead
        "mode": "local",
    },
    "plugins": {
        "web_search": {
            # Opt-in.  Off by default — nothing leaves the machine unless this is true.
            "enabled": False,
            # Provider: "duckduckgo" (free, no key) or "tavily" (better quality, free tier).
            "provider": "duckduckgo",
            # api_key: leave blank and set TAVILY_API_KEY env var instead.
            "api_key": "",
        },
    },
}


@dataclass
class Config:
    """Merged, resolved configuration plus provenance for debugging."""

    data: dict[str, Any]
    project_root: Path
    global_path: Path = GLOBAL_CONFIG_PATH
    project_path: Path | None = None
    overrides: dict[str, Any] = field(default_factory=dict)

    # -- typed accessors for the hot paths -------------------------------------
    @property
    def model_string(self) -> str:
        return self.data["model"]["default"]

    @property
    def sandbox(self) -> dict[str, Any]:
        return self.data["sandbox"]

    @property
    def policy(self) -> dict[str, Any]:
        return self.data["policy"]

    @property
    def subagents(self) -> dict[str, Any]:
        return self.data["subagents"]

    @property
    def network_policy(self) -> str:
        """Resolved network policy for the sandbox (none|restricted|full)."""
        return self.data.get("sandbox", {}).get("network", "none")

    @property
    def web_search_plugin(self) -> dict[str, Any]:
        """The [plugins.web_search] block with all defaults applied."""
        return self.data.get("plugins", {}).get("web_search", {})

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Read a value by ``section.key`` dotted path."""
        node: Any = self.data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge ``overlay`` onto a copy of ``base``."""
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` looking for a project marker.

    Prefers the directory containing ``.openlocal.toml``; otherwise the nearest
    ``.git`` root; otherwise the starting directory.
    """
    start = (start or Path.cwd()).resolve()
    for parent in [start, *start.parents]:
        if (parent / PROJECT_CONFIG_NAME).exists():
            return parent
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return start


def load_config(
    start: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Config:
    """Resolve the full layered config.

    ``overrides`` is the session layer (CLI flags), applied last.
    """
    project_root = find_project_root(start)
    project_path = project_root / PROJECT_CONFIG_NAME

    merged = copy.deepcopy(DEFAULTS)
    merged = _deep_merge(merged, _read_toml(GLOBAL_CONFIG_PATH))
    merged = _deep_merge(merged, _read_toml(project_path))
    if overrides:
        merged = _deep_merge(merged, overrides)

    return Config(
        data=merged,
        project_root=project_root,
        project_path=project_path if project_path.exists() else None,
        overrides=overrides or {},
    )


def write_global(data: dict[str, Any]) -> Path:
    """Persist (merge) values into the global config file."""
    GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_toml(GLOBAL_CONFIG_PATH)
    merged = _deep_merge(existing, data)
    with GLOBAL_CONFIG_PATH.open("wb") as fh:
        tomli_w.dump(merged, fh)
    return GLOBAL_CONFIG_PATH


def write_project(project_root: Path, data: dict[str, Any]) -> Path:
    """Persist (merge) values into the project ``.openlocal.toml``."""
    path = project_root / PROJECT_CONFIG_NAME
    existing = _read_toml(path)
    merged = _deep_merge(existing, data)
    with path.open("wb") as fh:
        tomli_w.dump(merged, fh)
    return path


def set_key(dotted_key: str, value: Any, *, scope: str, project_root: Path) -> Path:
    """Set a dotted ``section.key`` value in the given scope's file."""
    parts = dotted_key.split(".")
    if len(parts) < 2:
        raise ValueError("Config keys must be 'section.key', e.g. 'model.default'.")
    nested: dict[str, Any] = {}
    cursor = nested
    for part in parts[:-1]:
        cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value
    if scope == "global":
        return write_global(nested)
    return write_project(project_root, nested)

"""Semantic code search using local Ollama embedding models.

Indexes project source files on the host and enables meaning-based search,
e.g. "where is Redis initialized?" without knowing the exact function name.

Embeddings are cached in ``.openlocal/embed_cache.json`` and only
recomputed when a file's mtime changes, making repeated searches fast.

Auto-detects the best available embedding model from your Ollama library
(priority order):
  1. snowflake-arctic-embed2:latest   (highest quality)
  2. nomic-embed-text-v2-moe:latest   (good quality, smaller)
  3. embeddinggemma:latest            (fallback)
  4. nomic-embed-text:latest          (fallback)

All processing is local — nothing leaves the machine.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Embedding model preference list — first one found in Ollama wins.
_EMBED_CANDIDATES = [
    "snowflake-arctic-embed2:latest",
    "nomic-embed-text-v2-moe:latest",
    "embeddinggemma:latest",
    "nomic-embed-text:latest",
]

# File types to index.
_INDEXABLE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".java", ".rs", ".cpp", ".c", ".h",
    ".rb", ".php", ".cs", ".swift", ".kt",
    ".md", ".yaml", ".yml", ".toml",
}

# Dirs that are never worth indexing.
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".openlocal", "dist", "build", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "coverage",
}

_MAX_FILE_BYTES = 100_000   # skip files larger than 100 KB
_CHUNK_LINES    = 50        # lines per embedding chunk
_CHUNK_OVERLAP  = 10        # overlapping lines between consecutive chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _detect_embed_model() -> str | None:
    """Return the first embedding model available in the local Ollama daemon."""
    try:
        resp = httpx.get(f"{_OLLAMA_HOST}/api/tags", timeout=3.0)
        resp.raise_for_status()
        available = {m["name"] for m in resp.json().get("models", [])}
        for candidate in _EMBED_CANDIDATES:
            if candidate in available:
                return candidate
    except Exception:
        pass
    return None


def _embed(text: str, model: str) -> list[float] | None:
    """Call the Ollama /api/embed endpoint and return the embedding vector."""
    try:
        resp = httpx.post(
            f"{_OLLAMA_HOST}/api/embed",
            json={"model": model, "input": text},
            timeout=60.0,
        )
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings", [])
        return embeddings[0] if embeddings else None
    except Exception:
        return None


def _should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in _SKIP_DIRS or part.endswith(".egg-info"):
            return True
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return True
    except OSError:
        return True
    return path.suffix not in _INDEXABLE_EXTS


def _chunk_file(path: Path) -> list[tuple[int, str]]:
    """Split a file into overlapping line-chunks. Returns [(line_start, text)]."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    if not lines:
        return []
    step    = max(1, _CHUNK_LINES - _CHUNK_OVERLAP)
    chunks  = []
    for i in range(0, len(lines), step):
        text = "\n".join(lines[i: i + _CHUNK_LINES])
        if text.strip():
            chunks.append((i + 1, text))
    return chunks


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class SemanticIndex:
    """Mtime-aware embedding index with a JSON disk cache."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.cache_path   = project_root / ".openlocal" / "embed_cache.json"
        self.embed_model: str | None = None
        self._chunks: list[dict[str, Any]] = []
        self.built = False

    # -- disk cache ------------------------------------------------------------

    def _load_cache(self) -> dict:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"model": None, "chunks": []}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps({"model": self.embed_model, "chunks": self._chunks}),
            encoding="utf-8",
        )

    # -- build -----------------------------------------------------------------

    def build(self) -> tuple[int, int]:
        """Index (or incrementally update) the project.

        Returns (newly_embedded, total_chunks).
        """
        self.embed_model = _detect_embed_model()
        if not self.embed_model:
            self.built = True
            return 0, 0

        cache = self._load_cache()
        # If model changed, discard old cache.
        self._chunks = cache.get("chunks", []) if cache.get("model") == self.embed_model else []

        # Key: (rel_path, line_start) → existing chunk
        cached_map: dict[tuple[str, int], dict] = {
            (c["path"], c["line_start"]): c for c in self._chunks
        }

        new_chunks: list[dict] = []
        newly_embedded = 0

        for fpath in sorted(self.project_root.rglob("*")):
            if not fpath.is_file() or _should_skip(fpath):
                continue
            rel = str(fpath.relative_to(self.project_root))
            try:
                mtime = fpath.stat().st_mtime
            except OSError:
                continue

            for line_start, chunk_text in _chunk_file(fpath):
                key = (rel, line_start)
                cached = cached_map.get(key)
                if cached and cached.get("mtime") == mtime:
                    new_chunks.append(cached)
                else:
                    emb = _embed(chunk_text, self.embed_model)
                    if emb:
                        new_chunks.append({
                            "path":       rel,
                            "line_start": line_start,
                            "text":       chunk_text,
                            "embedding":  emb,
                            "mtime":      mtime,
                        })
                        newly_embedded += 1

        self._chunks = new_chunks
        self._save_cache()
        self.built = True
        return newly_embedded, len(new_chunks)

    # -- search ----------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Return the top_k most semantically similar chunks."""
        if not self.embed_model or not self._chunks:
            return []
        q_emb = _embed(query, self.embed_model)
        if q_emb is None:
            return []
        scored = [
            (_cosine_sim(q_emb, c["embedding"]), c)
            for c in self._chunks
            if c.get("embedding")
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]


# ---------------------------------------------------------------------------
# Module-level singleton (one index per project_root)
# ---------------------------------------------------------------------------

_INDEX: SemanticIndex | None = None


def _get_index(project_root: Path) -> SemanticIndex:
    global _INDEX
    if _INDEX is None or _INDEX.project_root != project_root:
        _INDEX = SemanticIndex(project_root)
    return _INDEX


# ---------------------------------------------------------------------------
# LangChain tool factory
# ---------------------------------------------------------------------------

def build_semantic_search_tool(project_root: Path):
    """Return a configured ``semantic_search`` LangChain tool."""
    from langchain_core.tools import tool as lc_tool
    from openlocal.ui import console as ui

    index = _get_index(project_root)

    @lc_tool
    def semantic_search(query: str, top_k: int = 5) -> str:
        """Find code by MEANING — not by exact text match.

        Use this instead of grep when:
        - You don't know the exact function or variable name
        - You want to find code by intent: "where is authentication handled?"
        - You need to locate all places that do a similar operation
        - grep gives too many results or zero results

        The search uses a local embedding model (100%% private, offline).
        On the first call it builds an index of the workspace — this may
        take a few seconds for large codebases; subsequent calls are fast.

        Args:
            query:  Natural-language description of the code you're looking for.
            top_k:  Number of results to return (default 5).

        Returns:
            Most relevant code snippets with file paths and line numbers.
        """
        if not index.built:
            ui.info(f"Building semantic index (using {_detect_embed_model() or 'no model found'})…")
            new, total = index.build()
            if not index.embed_model:
                return (
                    "No embedding model found in Ollama. Pull one:\n"
                    "  ollama pull snowflake-arctic-embed2\n"
                    "  ollama pull nomic-embed-text-v2-moe"
                )
            ui.info(f"Index ready: {total} chunk(s) ({new} newly embedded) — model: {index.embed_model}")

        results = index.search(query, top_k=top_k)
        if not results:
            return f"No semantically similar code found for: {query!r}"

        parts = [f"Semantic search results for: {query!r}\n"]
        for i, chunk in enumerate(results, 1):
            snippet = chunk["text"][:600]
            parts.append(
                f"[{i}] {chunk['path']}  (line {chunk['line_start']})\n"
                f"```\n{snippet}\n```"
            )
        return "\n\n---\n\n".join(parts)

    return semantic_search

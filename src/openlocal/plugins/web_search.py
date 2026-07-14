"""Web Search plugin for OpenLocal CLI.

Opt-in only.  Disabled by default.  This module is never imported unless
``plugins.web_search.enabled = true`` is set in `.openlocal.toml`.

Two tools are exposed:
  search_web(query, rationale)   — privacy-sanitized DuckDuckGo/Tavily search
  read_url(url)                  — Jina Reader markdown-stripped page reader

Privacy pipeline (applied to EVERY query before it hits the network):
  1. Secret scan  → redact API keys, tokens, high-entropy strings
  2. Path strip   → replace /workspace/... paths with [LOCAL_PATH]
  3. Truncate     → cap query at 300 chars to avoid sending large code blobs

Only the sanitized query ever leaves the machine.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import tool

# Local path regex: /some/path/like/this  →  [LOCAL_PATH]
_PATH_RE = re.compile(r"(/[\w.\-]+){2,}")
# Cap search queries to prevent accidental code dumps
_MAX_QUERY_LEN = 300
# Cap page content returned to model (token-budget protection)
_MAX_PAGE_CHARS = 12_000  # ≈ 3 000 tokens


# ---------------------------------------------------------------------------
# Query sanitization
# ---------------------------------------------------------------------------

def _sanitize_query(raw: str) -> str:
    """Apply the 3-step privacy pipeline to a search query."""
    from openlocal.sandbox.secret_scan import scan

    # Step 1: redact secrets (API keys, tokens, high-entropy blobs)
    result = scan(raw, entropy=True)
    cleaned = result.redacted_text

    # Step 2: strip local filesystem paths
    cleaned = _PATH_RE.sub("[LOCAL_PATH]", cleaned)

    # Step 3: hard truncate
    if len(cleaned) > _MAX_QUERY_LEN:
        cleaned = cleaned[:_MAX_QUERY_LEN] + "…"

    return cleaned.strip()


# ---------------------------------------------------------------------------
# Search backends — priority cascade:
#   1. Jina AI (s.jina.ai)      — primary: free, no key, LLM-optimised markdown
#   2. SearxNG public instances — fallback: aggregates Google/Bing/GitHub/SO
#   3. DuckDuckGo               — last resort: local library, no HTTP needed
# ---------------------------------------------------------------------------

# SearxNG public instances that reliably allow JSON API access.
# Shuffled on each call so no single server gets hammered.
_SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://searx.tiekoetter.com",
    "https://search.mdosch.de",
    "https://paulgo.io",
    "https://searxng.world",
]


def _search_jina(query: str) -> str:
    """Jina AI public search endpoint — returns LLM-ready Markdown directly.

    Free, no API key, no scraping. Built specifically for agents.
    Rate limit is very generous for single-user CLI usage.
    """
    import httpx
    from urllib.parse import quote

    url = f"https://s.jina.ai/{quote(query)}"
    headers = {
        "Accept": "text/markdown",
        # Bias toward technical sources (optional, can be removed)
        "X-Site": "stackoverflow.com, github.com, docs.python.org, developer.mozilla.org",
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
    except httpx.ConnectError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or "certificate verify failed" in str(e):
            # Retry with certificate verification disabled
            resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True, verify=False)
        else:
            raise
    resp.raise_for_status()
    return resp.text  # Already perfect Markdown — no parsing needed


def _search_searxng(query: str) -> list[dict[str, str]]:
    """Query public SearxNG instances — aggregates Google/Bing/GitHub/SO.

    Shuffles the instance list so no single server is spammed.
    Tries each instance in turn; skips on timeout or error.
    """
    import httpx
    import random

    instances = list(_SEARXNG_INSTANCES)
    random.shuffle(instances)

    for instance in instances:
        try:
            params = {
                "q": query,
                "format": "json",
                "engines": "google,github,stackoverflow",
                "language": "en",
            }
            try:
                resp = httpx.get(
                    f"{instance}/search",
                    params=params,
                    timeout=6,
                    follow_redirects=True,
                )
            except httpx.ConnectError as e:
                if "CERTIFICATE_VERIFY_FAILED" in str(e) or "certificate verify failed" in str(e):
                    resp = httpx.get(
                        f"{instance}/search",
                        params=params,
                        timeout=6,
                        follow_redirects=True,
                        verify=False,
                    )
                else:
                    raise
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])[:5]
                if results:
                    return [
                        {
                            "title": r.get("title", ""),
                            "body": r.get("content", ""),
                            "href": r.get("url", ""),
                        }
                        for r in results
                    ]
        except Exception:
            continue  # server down or rate-limited — try the next one

    raise RuntimeError("All SearxNG instances failed or returned no results.")


def _search_duckduckgo(query: str) -> list[dict[str, str]]:
    """DuckDuckGo via the duckduckgo-search library — no HTTP, no API key."""
    from duckduckgo_search import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=5))


def _call_search(query: str, config: dict[str, Any]) -> str:
    """Run the 3-tier cascade: Jina → SearxNG → DuckDuckGo.

    If the user explicitly set provider="tavily", use Tavily instead
    (requires TAVILY_API_KEY env var).

    Returns a formatted Markdown string ready for the model.
    """
    provider = config.get("provider", "jina")

    # --- Explicit Tavily override ---
    if provider == "tavily":
        import os, httpx
        api_key = os.environ.get("TAVILY_API_KEY", config.get("api_key", ""))
        if not api_key:
            raise RuntimeError(
                "Tavily provider requires TAVILY_API_KEY env var or "
                "[plugins.web_search] api_key in .openlocal.toml"
            )
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": 5},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("results", [])
        return _format_results(
            [{"title": r.get("title", ""), "body": r.get("content", ""), "href": r.get("url", "")}
             for r in items]
        )

    # --- Tier 1: Jina AI (primary) ---
    errors: list[str] = []
    try:
        result = _search_jina(query)
        if result.strip():
            return result
        errors.append("Jina returned empty response")
    except Exception as exc:
        errors.append(f"Jina: {exc}")

    # --- Tier 2: SearxNG (fallback) ---
    try:
        results = _search_searxng(query)
        if results:
            return _format_results(results)
        errors.append("SearxNG returned empty results")
    except Exception as exc:
        errors.append(f"SearxNG: {exc}")

    # --- Tier 3: DuckDuckGo (last resort) ---
    try:
        results = _search_duckduckgo(query)
        if results:
            return _format_results(results)
        errors.append("DuckDuckGo returned empty results")
    except Exception as exc:
        errors.append(f"DuckDuckGo: {exc}")

    raise RuntimeError(
        "All search backends failed:\n" + "\n".join(f"  • {e}" for e in errors)
    )


def _format_results(results: list[dict[str, str]]) -> str:
    if not results:
        return "No results found."
    parts = []
    for i, r in enumerate(results, 1):
        title = r.get("title") or r.get("href", "")
        body = r.get("body", "")[:400]
        url = r.get("href", "")
        parts.append(f"**{i}. {title}**\n{body}\nSource: {url}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Plugin tools
# ---------------------------------------------------------------------------

@tool
def search_web(query: str, rationale: str) -> str:
    """Search the internet for documentation, error codes, or library information.

    ## LAST RESORT RULES — read before using:
    1. Only call this for EXTERNAL information: API changes, unknown error codes,
       new libraries, CVE details, or official documentation.
    2. Do NOT use for basic Python/JS syntax — you already know this.
    3. GENERALIZE your query — never include local file names, variable names,
       or line numbers in the query.
       BAD:  "TypeError in my_custom_user_auth.py line 44"
       GOOD: "TypeError pydantic v2 model_dump method signature"
    4. Always provide a `rationale` explaining WHY you need external info.

    Args:
        query: A generalized search query (no local paths, no variable names).
        rationale: One sentence explaining why local context is insufficient.

    Returns:
        Top search results as formatted Markdown snippets.
    """
    # Lazy import to avoid loading config at module import time
    from openlocal.config import load_config
    cfg = load_config()
    plugin_cfg = cfg.get("plugins.web_search") or {}

    safe_query = _sanitize_query(query)
    if safe_query != query:
        # Log that sanitization changed something (UI-safe, no secret revealed)
        try:
            from openlocal.ui import console as ui
            ui.warn(f"search query sanitized before sending (removed secrets/paths)")
        except Exception:
            pass

    try:
        results = _call_search(safe_query, plugin_cfg)
        return results
    except Exception as exc:
        return f"Search failed: {exc}\nTip: check network access or switch provider in .openlocal.toml"


@tool
def read_url(url: str) -> str:
    """Fetch a webpage and return its content as clean Markdown (no HTML, no ads).

    Use this to read a specific StackOverflow answer, GitHub issue, or docs page
    that was returned by search_web.

    The page is processed through Jina Reader which strips HTML, navigation,
    sidebars, and ads — returning only the readable text content.
    Content is capped at ~3 000 tokens to protect your context window.

    Do NOT use this to read local files — use `read_file` for that.

    Args:
        url: The full URL of the page to read (https://...).

    Returns:
        The page content as clean Markdown text, truncated if very long.
    """
    import httpx

    if not url.startswith("http"):
        return "Error: only http/https URLs are supported."

    jina_url = f"https://r.jina.ai/{url}"
    try:
        resp = httpx.get(
            jina_url,
            headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
            timeout=20,
            follow_redirects=True,
        )
        resp.raise_for_status()
        content = resp.text
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    if len(content) > _MAX_PAGE_CHARS:
        content = content[:_MAX_PAGE_CHARS]
        content += f"\n\n[Content truncated at {_MAX_PAGE_CHARS} characters. Use search_web to find a more specific page.]"

    return content


# ---------------------------------------------------------------------------
# Plugin entry point — called by agent/build.py
# ---------------------------------------------------------------------------

def get_tools() -> list:
    """Return the list of tools this plugin contributes."""
    return [search_web, read_url]

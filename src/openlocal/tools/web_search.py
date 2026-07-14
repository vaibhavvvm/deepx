"""Web search tool using DuckDuckGo (no API key required).

Only active when the agent is started with ``--mode web``.  Searches are
anonymous but do leave the machine — the UI prints a privacy notice at
startup when this mode is selected, just like switching to a cloud model.

Install:
    pipx install 'openlocal-cli[web]'
    # or: pip install duckduckgo-search
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for documentation, error messages, CVEs, or API references.

    Use this when:
    - You need to look up a library's documentation or API
    - You encounter an unfamiliar error message or exception
    - You need to check for known vulnerabilities (CVE database)
    - You need external context that isn't in the codebase

    Args:
        query:       Natural language search query.
        max_results: Maximum number of results to return (default 5).

    Returns:
        Formatted search results with title, snippet, and URL for each hit.
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return (
            "Web search requires the 'web' extra:\n"
            "  pipx install 'openlocal-cli[web]'\n"
            "  # or: pip install duckduckgo-search"
        )

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return f"No web results found for: {query!r}"

        parts = [f"Web search results for: {query!r}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            body = (r.get("body") or "").strip()
            url = r.get("href", "")
            parts.append(f"[{i}] {title}\n{body}\nURL: {url}")

        return "\n\n---\n\n".join(parts)

    except Exception as exc:
        return f"Web search failed: {exc}"

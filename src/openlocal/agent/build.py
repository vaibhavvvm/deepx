"""Assemble the deep agent from a resolved spec, sandbox, config, and mode."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from openlocal.providers.base import ProviderSpec, resolve_model

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Below this context window we consider a model "small" and trim its tool
# surface: no subagent delegation, since spawning subagents compounds
# tool-calling failures on 4B-8B models.
SMALL_MODEL_CONTEXT_THRESHOLD = 16384


@cache
def load_prompt(name: str) -> str:
    """Read a bundled prompt markdown file by filename."""
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def _should_enable_subagents(spec: ProviderSpec, config: dict) -> bool:
    if not config.get("subagents", {}).get("enable_task_delegation", True):
        return False
    if not spec.supports_tool_calling:
        return False
    if spec.context_window < SMALL_MODEL_CONTEXT_THRESHOLD:
        return False
    return True


def _build_extra_tools(mode: str, project_root: Path) -> list:
    """Construct additional tools based on the active mode.

    Modes:
        local  — no extra tools (default, fully private)
        smart  — adds semantic_search (local embeddings, still fully private)
        web    — adds semantic_search + web_search (DuckDuckGo queries leave machine)
    """
    from openlocal.ui import console as ui

    from openlocal.tools.scratchpad import scratchpad
    from openlocal.tools.edit_tools import replace_in_file, read_file_outline
    tools: list = [scratchpad, replace_in_file, read_file_outline]

    if mode in ("smart", "web"):
        try:
            from openlocal.tools.semantic_search import build_semantic_search_tool
            tools.append(build_semantic_search_tool(project_root))
        except Exception as exc:
            ui.warn(f"semantic_search unavailable: {exc}")

    if mode == "web":
        try:
            from openlocal.tools.web_search import web_search
            tools.append(web_search)
        except Exception as exc:
            ui.warn(f"web_search unavailable: {exc}")

    return tools


def _mode_prompt_suffix(mode: str) -> str:
    """Return a system-prompt appendix that tells the model which extra tools exist."""
    if mode == "smart":
        return (
            "\n\n## Extra tools available in this session\n"
            "- `semantic_search(query)` — find code by MEANING (not text). "
            "Use this before grep when you don't know the exact symbol name.\n"
        )
    if mode == "web":
        return (
            "\n\n## Extra tools available in this session\n"
            "- `semantic_search(query)` — find code by MEANING (not text). "
            "Use this before grep when you don't know the exact symbol name.\n"
            "- `web_search(query)` — DuckDuckGo web search. Use this to look up "
            "documentation, error messages, CVEs, or any external information "
            "not present in the codebase.\n"
            "\nNEVER call a tool that is not in this list or the standard sandbox tools.\n"
        )
    # local mode: explicitly remind the model about the correct tool names
    return (
        "\n\n## Available tools reminder\n"
        "You have: execute, read_file, write_file, edit_file, ls, grep, "
        "upload_files, download_files. There is NO 'search', 'browse', or "
        "'web_search' tool in this mode. Use grep for text search.\n"
    )


def build_agent(
    spec: ProviderSpec,
    sandbox: Any,
    config: Any,           # Config object (preferred) or raw dict for compat
    *,
    project_root: Path,
    checkpointer: Any = None,
    mode: str = "local",
):
    """Build a compiled deep agent.

    Args:
        spec:         Resolved provider/model spec.
        sandbox:      A ``SandboxBackendProtocol`` implementation (Docker backend).
        config:       Config object or raw config dict.
        project_root: Repo root — used to load ``AGENTS.md`` conventions and
                      build the semantic index.
        checkpointer: Optional LangGraph checkpointer for resumability.
        mode:         ``"local"`` (default) | ``"smart"`` | ``"web"`` | ``"compose"``.
    """
    from deepagents import create_deep_agent

    from openlocal.agent.memory import compose_system_prompt
    from openlocal.agent.subagents import build_subagents

    # Accept both a Config object and a raw dict (backwards compat)
    config_dict: dict = config.data if hasattr(config, "data") else config
    network_policy: str = (
        config.network_policy if hasattr(config, "network_policy")
        else config_dict.get("sandbox", {}).get("network", "none")
    )
    plugin_cfg: dict = (
        config.web_search_plugin if hasattr(config, "web_search_plugin")
        else config_dict.get("plugins", {}).get("web_search", {})
    )

    model = resolve_model(spec)

    # System prompt = base + project conventions + mode tool hints
    base_system = load_prompt("system.md") + _mode_prompt_suffix(mode)
    system_prompt = compose_system_prompt(base_system, project_root)

    subagents = []
    if _should_enable_subagents(spec, config_dict):
        role_models = config_dict.get("subagents", {}).get("models", {})
        subagents = build_subagents(role_models)

    # Middleware — outermost first:
    #   1. redaction  (cloud specs only) — scrub secrets before anything else
    #   2. fallback   (if [model] fallback set) — swap model on timeout/error
    #   3. retry      (small/unreliable models) — re-prompt on malformed tool call
    middleware = []
    if not spec.is_local:
        from openlocal.agent.redaction import CloudRedactionMiddleware
        middleware.append(CloudRedactionMiddleware())

    from openlocal.agent.fallback import build_fallback_middleware
    fb = build_fallback_middleware(config)
    if fb is not None:
        middleware.append(fb)

    if not spec.supports_tool_calling or spec.context_window < SMALL_MODEL_CONTEXT_THRESHOLD:
        from openlocal.agent.retry import ToolCallRetryMiddleware
        middleware.append(ToolCallRetryMiddleware())

    # Loop-breaker: detect 3+ identical back-to-back tool calls → state-break
    from openlocal.agent.retry import LoopBreakerMiddleware
    middleware.append(LoopBreakerMiddleware())

    from openlocal.agent.repair_loop import RepairLoopMiddleware
    middleware.append(RepairLoopMiddleware())

    # Build extra host-side tools (semantic search, web search legacy mode).
    extra_tools = _build_extra_tools(mode, project_root)

    # --- Plugin injection (the opt-in pattern) ---
    # Inject web_search plugin ONLY if:
    #   (a) explicitly enabled in config AND
    #   (b) network is NOT "none" (air-gap safety invariant)
    ws_enabled = plugin_cfg.get("enabled", False)
    if ws_enabled and network_policy != "none":
        try:
            from openlocal.plugins.web_search import get_tools as ws_tools
            extra_tools = list(extra_tools) + ws_tools()
            from openlocal.ui import console as ui
            ui.info(
                "[dim cyan]Plugin: web_search active — "
                f"provider={plugin_cfg.get('provider', 'duckduckgo')}. "
                "Queries are sanitized before leaving the machine.[/dim cyan]"
            )
        except Exception as exc:
            from openlocal.ui import console as ui
            ui.warn(f"web_search plugin failed to load: {exc}")
    elif ws_enabled and network_policy == "none":
        from openlocal.ui import console as ui
        ui.warn(
            "web_search plugin is enabled in config but network=none — "
            "plugin disabled for this session (air-gap invariant). "
            "Use --network restricted to allow web search."
        )

    # Try to pass extra_tools to create_deep_agent; fall back gracefully if
    # the installed deepagents version doesn't support the kwarg.
    base_kwargs: dict[str, Any] = dict(
        model=model,
        backend=sandbox,
        system_prompt=system_prompt,
        subagents=subagents,
        middleware=middleware,
        checkpointer=checkpointer,
    )

    if extra_tools:
        try:
            return create_deep_agent(**base_kwargs, tools=extra_tools)
        except TypeError:
            try:
                return create_deep_agent(**base_kwargs, extra_tools=extra_tools)
            except TypeError:
                from openlocal.ui import console as ui
                ui.warn(
                    f"{len(extra_tools)} extra tool(s) could not be registered "
                    "(deepagents version does not expose a 'tools' kwarg). "
                    "Upgrade: pip install -U deepagents"
                )

    return create_deep_agent(**base_kwargs)

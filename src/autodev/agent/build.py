"""Assemble the deep agent from a resolved spec, sandbox, and config."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from autodev.providers.base import ProviderSpec, resolve_model

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Below this context window we consider a model "small" and trim its tool
# surface: no subagent delegation, since spawning subagents compounds
# tool-calling failures on 4B-8B models (blueprint 4.5).
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


def build_agent(
    spec: ProviderSpec,
    sandbox: Any,
    config: dict,
    *,
    project_root: Path,
    checkpointer: Any = None,
):
    """Build a compiled deep agent.

    Args:
        spec: resolved provider/model spec.
        sandbox: a ``SandboxBackendProtocol`` implementation (Docker backend).
        config: the merged config dict.
        project_root: repo root, used to load ``AGENTS.md`` conventions.
        checkpointer: optional LangGraph checkpointer for resumability.
    """
    from deepagents import create_deep_agent

    from autodev.agent.memory import compose_system_prompt
    from autodev.agent.subagents import build_subagents

    model = resolve_model(spec)
    system_prompt = compose_system_prompt(load_prompt("system.md"), project_root)

    subagents = []
    if _should_enable_subagents(spec, config):
        role_models = config.get("subagents", {}).get("models", {})
        subagents = build_subagents(role_models)

    # Middleware order matters (first = outermost):
    #   1. redaction   -- scrub secrets before anything else touches the request
    #   2. fallback    -- swap to the fallback model on timeout/error
    #   3. retry       -- re-prompt once on a malformed tool call
    middleware = []
    if not spec.is_local:
        from autodev.agent.redaction import CloudRedactionMiddleware

        middleware.append(CloudRedactionMiddleware())

    from autodev.agent.fallback import build_fallback_middleware

    fb = build_fallback_middleware(config)
    if fb is not None:
        middleware.append(fb)

    # Small/unreliable models benefit most from the structured retry.
    if not spec.supports_tool_calling or spec.context_window < SMALL_MODEL_CONTEXT_THRESHOLD:
        from autodev.agent.retry import ToolCallRetryMiddleware

        middleware.append(ToolCallRetryMiddleware())

    return create_deep_agent(
        model=model,
        backend=sandbox,
        system_prompt=system_prompt,
        subagents=subagents,
        middleware=middleware,
        checkpointer=checkpointer,
    )

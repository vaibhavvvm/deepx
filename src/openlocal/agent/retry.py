"""Structured retry and loop-breaker middleware for small local models.

Two problems on 4B–8B models:

1. **Malformed JSON tool calls** – LangChain surfaces these on
   ``AIMessage.invalid_tool_calls``. We re-prompt once with the parse error.
   → :class:`ToolCallRetryMiddleware`

2. **Infinite identical loops** – After a failed grep/execute the model
   repeats the *exact same* tool call three times and never recovers.
   → :class:`LoopBreakerMiddleware`

Both are wired in ``agent/build.py`` — retry first, then loop-breaker.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable

from langchain_core.messages import HumanMessage
from langchain.agents.middleware import AgentMiddleware
from openlocal.ui import console as ui

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _first_message(response):
    result = getattr(response, "result", None)
    if result:
        return result[0]
    # Some handlers return an AIMessage directly.
    return response


def _invalid_tool_calls(ai_message) -> list:
    return list(getattr(ai_message, "invalid_tool_calls", None) or [])


# ---------------------------------------------------------------------------
# 1.  Malformed JSON retry
# ---------------------------------------------------------------------------

class ToolCallRetryMiddleware(AgentMiddleware):
    """Re-prompt once when the model emits an unparseable tool call.

    Kept dependency-light (subclassing AgentMiddleware) so it works
    across deepagents/langchain middleware versions.
    """

    name = "openlocal_toolcall_retry"

    def __init__(self, max_retries: int = 1):
        super().__init__()
        self.max_retries = max_retries

    def wrap_model_call(self, request, handler: Callable):  # type: ignore[override]
        response = handler(request)
        attempts = 0
        while attempts < self.max_retries:
            ai = _first_message(response)
            bad = _invalid_tool_calls(ai)
            if not bad:
                return response
            attempts += 1
            errors = "; ".join(
                f"{c.get('name', '?')}: {c.get('error', 'parse error')}" for c in bad
            )
            ui.warn(f"malformed tool call — retrying with schema error ({errors})")
            nudge = HumanMessage(
                content=(
                    "Your previous tool call could not be parsed:\n"
                    f"{errors}\n\n"
                    "Re-issue the tool call with strictly valid JSON arguments "
                    "matching the tool's schema. Do not add prose around it."
                )
            )
            retry_request = request.override(messages=[*request.messages, ai, nudge])
            response = handler(retry_request)
        return response


# ---------------------------------------------------------------------------
# 2.  Infinite-loop state breaker  (MiMo-Code "Constrained Syntax" pattern)
# ---------------------------------------------------------------------------

def _tool_call_fingerprint(ai_message) -> str | None:
    """Return a stable hash of the tool name + args, or None if no tool call."""
    calls = getattr(ai_message, "tool_calls", None) or []
    if not calls:
        return None
    parts = []
    for c in calls:
        name = c.get("name", "")
        args = str(sorted(c.get("args", {}).items()))
        parts.append(f"{name}:{args}")
    key = "|".join(parts)
    return hashlib.md5(key.encode()).hexdigest()


def _count_repeated_fingerprint(messages) -> tuple[str | None, int]:
    """Scan history backwards to find the most-recently repeated tool fingerprint.

    Returns (fingerprint, count).  Stops as soon as it finds a different
    fingerprint or a non-AI message.
    """
    fingerprint_counts: Counter[str] = Counter()
    last_fp: str | None = None

    for msg in reversed(messages):
        if getattr(msg, "type", None) != "ai":
            break
        fp = _tool_call_fingerprint(msg)
        if fp is None:
            break
        if last_fp is None:
            last_fp = fp
        if fp != last_fp:
            break
        fingerprint_counts[fp] += 1

    if last_fp:
        return last_fp, fingerprint_counts[last_fp]
    return None, 0


class LoopBreakerMiddleware(AgentMiddleware):
    """Detect identical back-to-back failed tool calls and inject a state-break.

    If the model issues the *exact same* tool call 3 times in a row (same tool
    name AND same arguments), it is stuck in an autoregressive loop.  We
    intercept and force it to rethink.
    """

    name = "openlocal_loop_breaker"

    def __init__(self, max_repeats: int = 3):
        super().__init__()
        self.max_repeats = max_repeats

    def wrap_model_call(self, request, handler: Callable):
        response = handler(request)
        ai_msg = _first_message(response)
        fp = _tool_call_fingerprint(ai_msg)
        if fp is None:
            return response  # no tool call → nothing to track

        # Count the same fingerprint in recent history + this new response
        _, prior_count = _count_repeated_fingerprint(request.messages)
        total_repeats = prior_count + 1

        if total_repeats >= self.max_repeats:
            tool_name = (getattr(ai_msg, "tool_calls", None) or [{}])[0].get("name", "unknown")
            ui.warn(
                f"loop-breaker: '{tool_name}' repeated {total_repeats}× identically — "
                "injecting state-break"
            )
            break_msg = HumanMessage(
                content=(
                    f"⚠ SYSTEM WARNING: You have issued the exact same failing "
                    f"'{tool_name}' call {total_repeats} times in a row.\n\n"
                    "You are in an infinite loop. You MUST do one of:\n"
                    "1. Change your tool entirely (e.g. use `execute` with raw bash "
                    "instead of the grep tool).\n"
                    "2. Change your syntax or arguments substantially.\n"
                    "3. Use the `scratchpad` tool to rethink your entire approach "
                    "before trying again.\n\n"
                    "Do NOT repeat the same call again."
                )
            )
            restart_request = request.override(
                messages=[*request.messages, ai_msg, break_msg]
            )
            return handler(restart_request)

        return response
